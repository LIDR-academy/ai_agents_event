"""Session 11 — two-stage synthesis of the per-task hours.

The per-task hours estimator (:mod:`app.generation.rag.task_hours`) averages the
nearest historical tasks into one weighted-consensus number. That is right when
the neighbours agree — and misleading when they do not: if one analog says 40h
and another 90h, the 65h midpoint reads as a confident point estimate while
hiding a 2× disagreement. Synthesis makes the disagreement first-class:

* Stage 1 — DETERMINISTIC anchor: the weighted consensus already computed by
  ``task_hours._consensus`` (reused, not re-implemented) plus its dispersion.
* Stage 2 — JUDGEMENT: only when the dispersion crosses the contradiction
  threshold, emit an hour :class:`HourRange` (low..high) with a reason naming the
  conflict. An optional cheap LLM phrases the reason; a deterministic fallback
  (min/max of the neighbours + a templated reason) runs when the LLM is disabled
  or unavailable, so the range is always produced offline.

The point ``estimated_hours`` is left untouched (the human still edits one
number); the range travels alongside it as the honest-uncertainty signal.
"""

from __future__ import annotations

import asyncio

import structlog

from app.generation.rag.schemas import HourRange, TaskNeighbor

log = structlog.get_logger()


def is_contradiction(dispersion: float | None, threshold: float) -> bool:
    """True when the neighbour spread crosses the contradiction threshold."""
    return dispersion is not None and dispersion > threshold


def _deterministic_range(neighbors: list[TaskNeighbor]) -> HourRange:
    """Fallback range: the min/max of the neighbour hours, conflict named."""
    hours = sorted(n.estimated_hours for n in neighbors)
    low, high = hours[0], hours[-1]
    return HourRange(
        low=low,
        high=high,
        reason=(
            f"historical sources disagree ({low}h vs {high}h across "
            f"{len(neighbors)} analogs); estimate depends on scope not yet pinned down"
        ),
    )


def _reason_system_prompt() -> str:
    return (
        "You are an estimation analyst. The historical analogs for a task disagree "
        "on effort. Given the low and high hour figures and the analog snippets, write "
        "ONE short sentence explaining the most likely reason for the spread (e.g. a "
        "scope or integration difference), so a human can resolve it. Do not change the "
        "numbers; only explain the disagreement."
    )


async def synthesize_range(
    neighbors: list[TaskNeighbor],
    dispersion: float | None,
    *,
    threshold: float,
    use_llm: bool = False,
    model: str | None = None,
) -> HourRange | None:
    """Return an :class:`HourRange` when the neighbours contradict, else ``None``.

    Deterministic by default (``use_llm=False``): the range is the neighbour
    min/max and the reason is templated. With ``use_llm=True`` a cheap model
    phrases the reason; on any failure it degrades to the deterministic reason.
    """
    if not neighbors or not is_contradiction(dispersion, threshold):
        return None

    base = _deterministic_range(neighbors)
    if not use_llm or not model:
        return base

    from app.dependencies import get_llm_wrapper

    snippets = "; ".join(f"{n.estimated_hours}h (dist {n.distance:.2f})" for n in neighbors)
    user_message = (
        f"Task analogs range from {base.low}h to {base.high}h. Analogs: {snippets}.\n"
        "Explain the disagreement in one sentence."
    )
    try:
        from pydantic import BaseModel, Field

        class _Reason(BaseModel):
            reason: str = Field(description="One-sentence explanation of the hour spread.")

        wrapper = get_llm_wrapper()
        reason, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_reason_system_prompt(),
            user_message=user_message,
            response_model=_Reason,
            model_override=model,
        )
        return HourRange(low=base.low, high=base.high, reason=reason.reason)
    except Exception as exc:  # noqa: BLE001 — the deterministic range is the floor.
        log.warning("synthesis_reason_failed", error=str(exc)[:200])
        return base
