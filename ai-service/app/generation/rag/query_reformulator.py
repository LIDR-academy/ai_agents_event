"""Query understanding: turn a raw meeting transcript into a structured brief.

A transcript is NOT a query. Embedding ~600 tokens of digression yields a vector
that is the semantic average of several intents plus conversational noise, which
produces compressed distances and irrelevant hits (see ``arquitectura-actual.md``,
fallo 1). This stage distills the transcript into an :class:`EstimationQuery` and
then composes a short, technical, English search string aligned with the corpus.

Both the primary extraction and the degraded fallback go through ``LLMWrapper``
(Instructor + LiteLLM), not the raw OpenAI Responses API — the wrapper already
owns retries, fallback, cost tracking and structured-output re-prompting.
"""

from __future__ import annotations

import asyncio

import structlog

from app.config import get_settings
from app.generation.rag.errors import ReformulationError
from app.generation.rag.schemas import EstimationQuery

log = structlog.get_logger()

_SYSTEM_PROMPT = (
    "You are a software-delivery analyst. Extract a structured project brief from "
    "a raw, messy client meeting transcript. Capture ONLY what the client wants to "
    "build and the constraints that bound it; ignore small talk, anecdotes and "
    "digressions. Normalise everything to concise technical English regardless of "
    "the transcript language. Leave a field empty/unknown when the transcript gives "
    "no evidence for it — never invent technologies, sectors or regulations."
)

_FALLBACK_SYSTEM_PROMPT = (
    "Rewrite the following client meeting transcript as a single short technical "
    "search query in English describing the software to build. One sentence, no "
    "preamble."
)


async def reformulate_query(transcript: str) -> EstimationQuery:
    """Distill a transcript into a structured :class:`EstimationQuery`.

    Parameters
    ----------
    transcript:
        Raw free-text transcript of a client meeting.

    Returns
    -------
    EstimationQuery
        The structured brief. On the primary path every field the model could
        ground is populated; on the degraded fallback only ``function`` is set.

    Raises
    ------
    ReformulationError
        If both the structured extraction and the free-text fallback fail.
    """
    from app.dependencies import get_llm_wrapper

    settings = get_settings()
    wrapper = get_llm_wrapper()

    try:
        query, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=_SYSTEM_PROMPT,
            user_message=transcript,
            response_model=EstimationQuery,
            model_override=settings.REFORMULATION_MODEL,
            # gpt-5-mini is also a reasoning model: give the same generous token
            # ceiling so reasoning tokens never starve the structured output on a
            # long/ambiguous transcript (the 4000 wrapper default would truncate).
            max_tokens=settings.GENERATION_MAX_TOKENS,
        )
        return query
    except Exception as exc:  # noqa: BLE001 — degrade to a simpler rewrite.
        log.warning(
            "reformulation_fallback",
            reason="structured_extraction_failed",
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )

    try:
        result = await asyncio.to_thread(
            wrapper.complete,
            system_prompt=_FALLBACK_SYSTEM_PROMPT,
            user_message=transcript,
            model_override=settings.REFORMULATION_MODEL,
        )
        rewritten = (result.get("estimation") or "").strip()
        if not rewritten:
            raise ReformulationError("Fallback rewrite returned an empty query.")
        return EstimationQuery(function=rewritten)
    except ReformulationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ReformulationError("Query reformulation failed.") from exc


def compose_search_text(query: EstimationQuery) -> str:
    """Compose the short technical string to embed from an :class:`EstimationQuery`.

    Joins the grounded fields into one corpus-aligned phrase, e.g.
    ``"B2B payments marketplace platform with Stripe Connect, KYC, SAP for
    healthcare in Germany, BaFin-compliant"``. Empty fields are dropped.
    """
    parts: list[str] = [query.function.strip()] if query.function.strip() else []

    if query.technologies:
        parts.append("with " + ", ".join(query.technologies))
    if query.sector:
        parts.append(f"for {query.sector}")
    if query.country:
        parts.append(f"in {query.country}")
    if query.regulations:
        parts.append(", ".join(query.regulations) + "-compliant")

    return " ".join(parts).strip()
