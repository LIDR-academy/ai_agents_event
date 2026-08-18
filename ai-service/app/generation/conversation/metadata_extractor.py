"""Second-pass LLM call that extracts ``ProjectMetadata`` from each turn.

The conversational pipeline runs this AFTER the main estimation succeeds. We
keep the call narrow on purpose: a small/cheap model (``METADATA_EXTRACTOR_MODEL``)
with a short prompt that returns only durable facts about the project. The
result is then merged with the prior metadata (scalar overwrite + tech list
union) and stored back on the Session.

If extraction fails for any reason (LLM timeout, validator unrecoverable),
we log the failure and return the previous metadata unchanged. The conversation
keeps working — losing one turn of metadata refresh is acceptable.
"""

from __future__ import annotations

import structlog

from app.foundation.prompts.loader import render_metadata_extraction_prompt
from app.domain.schemas.estimation import EstimationResult
from app.foundation.llm.wrapper import LLMWrapper
from app.generation.conversation.models import ProjectMetadata

log = structlog.get_logger()


def update_metadata(
    *,
    previous: ProjectMetadata,
    transcript: str,
    result: EstimationResult,
    llm_wrapper: LLMWrapper,
    model: str,
) -> ProjectMetadata:
    """Run the extractor and return ``previous.merge_with(extracted)``.

    On failure: log + return ``previous`` so a single bad extraction doesn't
    cascade and freeze the session.
    """
    system_prompt, user_message = render_metadata_extraction_prompt(
        transcript=transcript,
        result=result,
        previous=previous,
    )

    try:
        extracted, meta = llm_wrapper.complete_structured_chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_model=ProjectMetadata,
            model_override=model,
            max_tokens=1000,
            max_retries=2,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "metadata_extraction_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return previous

    merged = previous.merge_with(extracted)
    log.info(
        "metadata_extraction_completed",
        model=meta["model"],
        latency_ms=meta["latency_ms"],
        project_name=merged.project_name,
        team_size=merged.assumed_team_size,
        tech_count=len(merged.mentioned_technologies),
    )
    return merged
