"""Session 11 — semantic hallucination gate over a grounded estimate.

``verify_citations`` (Session 11 pre-work) proves REFERENTIAL integrity: every
cited ``chunk_id`` was actually retrieved. That is necessary but not sufficient —
a line can cite a real ``<source>`` and still invent the number: the citation is
real, the figure is hallucinated. This module adds the SEMANTIC layer on top:

* :func:`numeric_anchor` — a DETERMINISTIC anchor: the historical hours the cited
  chunks carry (metadata ``estimated_hours`` or a figure parsed from the source
  text), converted to engineer-days. No LLM, no cost, no variance.
* :func:`judge_estimate` — a STRICT judge (one batched LLM call): for each
  grounded line, does the cited evidence actually support the claimed number and
  scope? Reuses :class:`LLMWrapper` (Instructor), never the raw Responses API.
* :func:`gate_line` — the GRADED gate: a pure combiner of the anchor and the
  verdict into ``grounded`` / ``degraded`` / ``insufficient``.
* :func:`gate_estimate` — the conductor: anchors every line, runs the judge once
  (unless ``use_judge=False``) and aggregates a :class:`HallucinationReport`.

The rule is deliberately asymmetric and teachable: a grounded line may estimate
LESS than its source (it is one slice of a historical component), but a line that
claims substantially MORE than the evidence it cites — or that the judge cannot
tie to that evidence — is a hallucination wearing a real citation, and is graded
``degraded``.
"""

from __future__ import annotations

import asyncio
import re

import structlog

from app.generation.rag.schemas import (
    Estimate,
    HallucinationReport,
    LineGate,
    LineVerdict,
    RetrievedChunk,
    TaskItem,
)

log = structlog.get_logger()

# Hours per engineer-day: the corpus records hours, the estimate speaks in days.
_HOURS_PER_DAY = 8

# Last "<number> h" / "hours" figure in a source text — the historical hours a
# budget component / task chunk carries when it is not in the flattened metadata.
_HOURS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:h|hrs|hours)\b", re.IGNORECASE)


def _chunk_hours(chunk: RetrievedChunk) -> float | None:
    """The historical hours a chunk records: metadata first, then parsed text."""
    if chunk.estimated_hours is not None:
        return float(chunk.estimated_hours)
    matches = _HOURS_RE.findall(chunk.content or "")
    return float(matches[-1]) if matches else None


def numeric_anchor(task: TaskItem, chunks_by_id: dict[str, RetrievedChunk]) -> float | None:
    """Deterministic anchor for a line, in engineer-days.

    Sums the historical hours of the chunks the line cites (its ceiling: a line
    should not derive more effort than the sources it names support) and converts
    to engineer-days. Returns ``None`` when none of the cited chunks carry hours.
    """
    hours = [
        h
        for ref in task.sources
        if (chunk := chunks_by_id.get(ref.chunk_id)) is not None
        and (h := _chunk_hours(chunk)) is not None
    ]
    if not hours:
        return None
    return sum(hours) / _HOURS_PER_DAY


def gate_line(
    task: TaskItem,
    module_name: str,
    anchor_days: float | None,
    verdict: LineVerdict | None,
    *,
    tolerance: float,
) -> LineGate:
    """Combine the deterministic anchor and the judge verdict for one line.

    Pure function (no I/O): the teachable unit of the gate. ``verdict`` is
    ``None`` when the judge did not run (numeric-only mode).
    """
    if not task.grounded:
        return LineGate(module=module_name, component=task.name, status="insufficient")

    deviation: float | None = None
    numeric_fail = False
    if anchor_days and anchor_days > 0 and task.engineer_days is not None:
        overage = (task.engineer_days - anchor_days) / anchor_days
        deviation = abs(task.engineer_days - anchor_days) / anchor_days
        # Only an OVER-estimate beyond tolerance is a hallucination signal: a line
        # may legitimately be a fraction of its parent component.
        numeric_fail = overage > tolerance

    judge_fail = verdict is not None and not verdict.entailed

    if numeric_fail or judge_fail:
        reasons = []
        if numeric_fail:
            reasons.append(
                f"claims {task.engineer_days}d but the cited evidence supports ~{anchor_days:.0f}d"
            )
        if judge_fail and verdict is not None:
            reasons.append(verdict.reason)
        return LineGate(
            module=module_name,
            component=task.name,
            status="degraded",
            numeric_deviation=deviation,
            reason="; ".join(r for r in reasons if r),
        )

    return LineGate(
        module=module_name,
        component=task.name,
        status="grounded",
        numeric_deviation=deviation,
    )


def _judge_system_prompt() -> str:
    return (
        "You are a strict estimation auditor. For each estimate line you are given "
        "the claimed effort in engineer-days and the VERBATIM evidence it cites from "
        "a historical budget. Decide, for each line, whether that evidence genuinely "
        "supports the claimed effort and scope.\n"
        "- entailed=true only when the cited evidence names the same work and its "
        "hours are consistent with the claim (a line may be a fraction of a larger "
        "component; that is fine).\n"
        "- entailed=false when the number is not supported by the evidence — e.g. the "
        "line claims far MORE than the source records, or the evidence is about "
        "different work. Default to false when in doubt.\n"
        "Echo back each line's module and component exactly so verdicts can be matched."
    )


def _judge_user_message(grounded: list[tuple[str, TaskItem]]) -> str:
    blocks = []
    for module_name, task in grounded:
        evidence = " | ".join(f'"{ref.evidence}"' for ref in task.sources)
        blocks.append(
            f"<line>\n"
            f"module: {module_name}\n"
            f"component: {task.name}\n"
            f"claimed_engineer_days: {task.engineer_days}\n"
            f"cited_evidence: {evidence}\n"
            f"</line>"
        )
    return (
        "Audit each of the following estimate lines. Return one verdict per line.\n\n"
        + "\n".join(blocks)
    )


async def judge_estimate(
    estimate: Estimate,
    *,
    model: str,
) -> dict[tuple[str, str], LineVerdict]:
    """Run the strict judge over every grounded line in ONE batched LLM call.

    Returns a ``{(module, component): LineVerdict}`` map. Empty when there are no
    grounded lines. A judge failure degrades gracefully to an empty map (the gate
    then relies on the deterministic anchor alone).
    """
    from app.dependencies import get_llm_wrapper

    grounded = [
        (module.name, task)
        for module in estimate.modules
        for task in module.tasks
        if task.grounded and task.sources
    ]
    if not grounded:
        return {}

    from pydantic import BaseModel, Field

    class _Panel(BaseModel):
        verdicts: list[LineVerdict] = Field(default_factory=list)

    wrapper = get_llm_wrapper()
    try:
        panel, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_judge_system_prompt(),
            user_message=_judge_user_message(grounded),
            response_model=_Panel,
            model_override=model,
        )
    except Exception as exc:  # noqa: BLE001 — a judge failure must not sink the gate.
        log.warning("hallucination_judge_failed", error=str(exc)[:200])
        return {}

    return {(v.module, v.component): v for v in panel.verdicts}


async def gate_estimate(
    estimate: Estimate,
    chunks: list[RetrievedChunk],
    *,
    tolerance: float,
    judge_model: str,
    use_judge: bool = True,
) -> HallucinationReport:
    """Grade every line of ``estimate`` grounded / degraded / insufficient.

    Combines the deterministic numeric anchor with a single batched judge call.
    Set ``use_judge=False`` for the anchor-only path (no LLM, no cost).
    """
    chunks_by_id = {str(chunk.id): chunk for chunk in chunks}
    verdicts = await judge_estimate(estimate, model=judge_model) if use_judge else {}

    lines: list[LineGate] = []
    grounded_lines = degraded_lines = insufficient_lines = 0
    for module in estimate.modules:
        for task in module.tasks:
            anchor = numeric_anchor(task, chunks_by_id)
            verdict = verdicts.get((module.name, task.name))
            gate = gate_line(task, module.name, anchor, verdict, tolerance=tolerance)
            lines.append(gate)
            if gate.status == "grounded":
                grounded_lines += 1
            elif gate.status == "degraded":
                degraded_lines += 1
            else:
                insufficient_lines += 1

    return HallucinationReport(
        total_lines=len(lines),
        grounded_lines=grounded_lines,
        degraded_lines=degraded_lines,
        insufficient_lines=insufficient_lines,
        lines=lines,
    )
