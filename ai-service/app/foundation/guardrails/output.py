"""Output guardrails: belt-and-suspenders on top of Pydantic model_validators.

The schema-level validators (in ``app/schemas/estimation.py``) are the first
line: when they raise, Instructor re-prompts the LLM up to ``max_retries``
times. If the LLM keeps disagreeing, Instructor raises and the request fails.

``enforce_scope_response`` is a *filter* (not an *exception*): it rewrites the
``summary`` when the LLM produced a low-confidence answer without the
``Out of scope:`` prefix. In practice the validator would have raised before
we got here, so this filter mostly handles edge cases at the boundary
(``confidence_pct == 30`` exactly, or future loosening of the threshold).
"""

from __future__ import annotations

import structlog

from app.domain.schemas.estimation import (
    LOW_CONFIDENCE_THRESHOLD,
    OUT_OF_SCOPE_PREFIX,
    EstimationResult,
    Phase,
)

log = structlog.get_logger()


_NOT_ESTIMATED_PHASE = Phase(
    name="Not estimated",
    duration_weeks=1,
    cost_eur=0,
    summary="Cannot be sized without more information about scope, integrations and team.",
)


def enforce_scope_response(result: EstimationResult) -> EstimationResult:
    """Rewrite the result if confidence is low and the summary does not declare it.

    Policy: ``filter`` — never raises, always returns a well-formed
    ``EstimationResult``. The user gets a clear message instead of an error.
    """
    is_low_confidence = result.confidence_pct < LOW_CONFIDENCE_THRESHOLD
    already_marked = result.summary.startswith(OUT_OF_SCOPE_PREFIX)

    if not is_low_confidence or already_marked:
        return result

    log.info(
        "enforce_scope_response_filtering",
        confidence_pct=result.confidence_pct,
        original_summary_chars=len(result.summary),
    )
    new_summary = (
        f"{OUT_OF_SCOPE_PREFIX} not enough information to estimate confidently. "
        f"Original model rationale: {result.summary[:400]}"
    )
    return EstimationResult(
        summary=new_summary[:1200],
        confidence_pct=result.confidence_pct,
        phases=[_NOT_ESTIMATED_PHASE],
        total_duration_weeks=1,
        total_cost_eur=0,
    )
