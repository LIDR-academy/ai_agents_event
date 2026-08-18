"""End-to-end RAG estimation orchestrator (Session 9).

Wires the four stages into the loop the project has been missing since day one:
``transcript → query understanding → retrieval → augmentation → generation``,
producing a grounded :class:`Estimate`. Generation goes through ``LLMWrapper``
(Instructor) — the same primitive the rest of the service uses — never the raw
Responses API.

Public functions keep the locked async signatures; the synchronous wrapper and
embedder calls are pushed to threads so the HTTP path never blocks the event
loop.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import structlog

from app.config import get_settings
from app.generation.rag.context_assembler import build_context_block, truncate_to_token_budget
from app.generation.rag.errors import GenerationError, MalformedEstimateError
from app.generation.rag.quality.augmentation import augment_chunks
from app.generation.rag.quality.hallucination import gate_estimate
from app.generation.rag.observability import log_stage
from app.generation.rag.prompt_builder import (
    build_structure_system_prompt,
    build_structure_user_message,
    build_system_prompt,
    build_user_message,
)
from app.generation.rag.query_reformulator import compose_search_text, reformulate_query
from app.generation.rag.retrieval.pipeline import retrieve
from app.generation.rag.schemas import Estimate, EstimationQuery
from app.generation.rag.validation import check_coherence, verify_citations

log = structlog.get_logger()

# Sectors present in the corpus; only filter retrieval when the reformulated
# brief names one of them (avoids over-filtering on free-text sector values).
_KNOWN_SECTORS = {
    "finance",
    "ecommerce",
    "healthcare",
    "industrial",
    "logistics",
    "education",
    "media",
    "government",
}


async def generate_estimate(
    context_block: str,
    structured_query: EstimationQuery,
    *,
    include_hours: bool = True,
) -> Estimate:
    """Generate a grounded :class:`Estimate` from an assembled context block.

    Parameters
    ----------
    context_block:
        The ``<source>`` XML block produced by the context assembler.
    structured_query:
        The reformulated project brief.
    include_hours:
        When ``False`` (Session 10 structure-only mode) the model returns the
        module → task structure without effort numbers; the hours are derived
        afterwards by per-task vector search.

    Returns
    -------
    Estimate
        The validated estimate as returned by the model (citations are checked
        by the caller, not here).

    Raises
    ------
    GenerationError
        If the LLM call fails irrecoverably.
    """
    return await _generate(context_block, structured_query, include_hours=include_hours)


async def _generate(
    context_block: str,
    structured_query: EstimationQuery,
    *,
    feedback: str | None = None,
    include_hours: bool = True,
) -> Estimate:
    """Single generation call. ``feedback`` appends a correction note for retries."""
    from app.dependencies import get_llm_wrapper

    settings = get_settings()
    wrapper = get_llm_wrapper()

    user_message = build_user_message(context_block, structured_query)
    if feedback:
        user_message += f"\n\n<correction>\n{feedback}\n</correction>"

    try:
        estimate, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=build_system_prompt(include_hours=include_hours),
            user_message=user_message,
            response_model=Estimate,
            model_override=settings.GENERATION_MODEL,
            reasoning_effort=settings.GENERATION_REASONING_EFFORT,
            # gpt-5 reasoning tokens count against max_tokens; the 4000 default
            # is exhausted by reasoning alone and truncates the JSON. See
            # Settings.GENERATION_MAX_TOKENS.
            max_tokens=settings.GENERATION_MAX_TOKENS,
        )
        return estimate
    except Exception as exc:  # noqa: BLE001
        raise GenerationError("Grounded estimate generation failed.") from exc


async def generate_structure(structured_query: EstimationQuery) -> Estimate:
    """Generate the module→task STRUCTURE as a free decomposition of the brief.

    Session 10: the wizard no longer grounds the structure in retrieved budgets
    (that impoverished the tree). This produces modules and tasks WITHOUT hours
    and WITHOUT sources — the hours are derived afterwards by per-task vector
    search (:mod:`app.generation.rag.task_hours`). No citation validation runs
    here because there are no sources to validate.
    """
    from app.dependencies import get_llm_wrapper

    settings = get_settings()
    wrapper = get_llm_wrapper()
    try:
        estimate, _meta = await asyncio.to_thread(
            wrapper.complete_structured,
            system_prompt=build_structure_system_prompt(),
            user_message=build_structure_user_message(structured_query),
            response_model=Estimate,
            model_override=settings.GENERATION_MODEL,
            reasoning_effort=settings.GENERATION_REASONING_EFFORT,
            max_tokens=settings.GENERATION_MAX_TOKENS,
        )
        return estimate
    except Exception as exc:  # noqa: BLE001
        raise GenerationError("Structure generation failed.") from exc


def _insufficient(explanation: str) -> Estimate:
    """Build the canonical insufficient-context estimate (no numbers)."""
    return Estimate(
        total_engineer_days=None,
        duration_weeks=None,
        confidence="insufficient",
        reasoning="Retrieval did not surface enough relevant historical budgets.",
        insufficient_context_explanation=explanation,
    )


def _current_request_id() -> str:
    """Reuse the HTTP request id bound by the middleware, or mint one."""
    bound = structlog.contextvars.get_contextvars().get("request_id")
    return bound or str(uuid4())


async def estimate_from_transcript(
    transcript: str,
    idempotency_key: str | None = None,
) -> Estimate:
    """Run the full transcript → grounded estimate pipeline.

    Steps: (optional) idempotency lookup → reformulate → embed → filtered
    retrieval (soft-fail short-circuits to an insufficient-context estimate) →
    token-budget truncation → context assembly → generation → citation
    validation (one corrective retry) → coherence check → (optional) cache.

    Parameters
    ----------
    transcript:
        Raw client meeting transcript.
    idempotency_key:
        When provided, a repeated call returns the cached estimate without
        re-running the pipeline (no LLM cost).

    Returns
    -------
    Estimate
        The grounded estimate (possibly ``confidence='insufficient'`` or
        downgraded to ``'low'`` if citations could not be repaired).

    Raises
    ------
    ReformulationError, RetrievalError, GenerationError, MalformedEstimateError
    """
    from app.dependencies import get_embedder, get_idempotency_store, get_token_encoder

    settings = get_settings()
    request_id = _current_request_id()
    store = get_idempotency_store()

    if idempotency_key:
        cached = await asyncio.to_thread(store.get, idempotency_key)
        if cached is not None:
            log.info("idempotency_hit", request_id=request_id, idempotency_key=idempotency_key)
            return cached

    # 1. Query understanding.
    with log_stage("reformulation", request_id):
        query = await reformulate_query(transcript)

    # 2. Compose + embed the canonical search text.
    with log_stage("embedding", request_id):
        search_text = compose_search_text(query)
        embedder = get_embedder()
        if embedder is None:
            raise GenerationError("Embedding service is not available (no OpenAI key).")
        query_embedding = await asyncio.to_thread(embedder.embed_one, search_text)

    # 3. Metadata-filtered retrieval with soft-fail. Search mode + reranking
    #    follow the runtime/settings defaults (Session 10), so the grounded
    #    estimate benefits from hybrid/rerank without changing this contract.
    from app.dependencies import get_runtime_retrieval_config

    runtime_retrieval = get_runtime_retrieval_config()
    search_mode = runtime_retrieval.effective_search_mode()
    rerank = runtime_retrieval.effective_rerank()
    sector = query.sector.lower().strip() if query.sector else None
    sectors = [sector] if sector in _KNOWN_SECTORS else None
    with log_stage(
        "retrieval", request_id, sectors=sectors, search_mode=search_mode, rerank=rerank
    ):
        retrieval = await retrieve(
            query_embedding=query_embedding,
            query_text=search_text,
            search_mode=search_mode,
            rerank=rerank,
            top_k=settings.RETRIEVAL_TOP_K,
            recall_k=settings.RETRIEVAL_RECALL_TOP_K,
            rerank_top_n=settings.RERANK_TOP_N,
            distance_threshold=settings.RETRIEVAL_DISTANCE_THRESHOLD,
            rrf_k=settings.RRF_K,
            sectors=sectors,
        )

    if retrieval.low_confidence:
        log.info(
            "retrieval_soft_fail", request_id=request_id, candidates=retrieval.candidates_evaluated
        )
        estimate = _insufficient(
            "No historical budgets crossed the relevance threshold for this project."
        )
        if idempotency_key:
            await asyncio.to_thread(store.set, idempotency_key, estimate)
        return estimate

    # 4. Truncate to the token budget (whole chunks only), augment, assemble.
    #    Session 11 augmentation (compress → edge-load reorder) runs on the kept
    #    chunks; it preserves ids, so citation verification below is unaffected.
    encoder = get_token_encoder()
    kept = truncate_to_token_budget(retrieval.chunks, settings.MAX_CONTEXT_TOKENS, encoder)
    if runtime_retrieval.effective_augmentation():
        kept = augment_chunks(
            kept,
            compress=settings.AUGMENTATION_COMPRESS,
            reorder=settings.AUGMENTATION_REORDER,
        )
    context_block = build_context_block(kept)

    # 5. Generate the grounded estimate.
    with log_stage("generation", request_id, sources=len(kept)):
        estimate = await generate_estimate(context_block, structured_query=query)

    # 6. Verify per-line citations; one corrective retry on dangling ids.
    retrieved_ids = {str(chunk.id) for chunk in kept}
    report = verify_citations(estimate, retrieved_ids)
    log.info(
        "citation_report",
        request_id=request_id,
        total_lines=report.total_lines,
        grounded_lines=report.grounded_lines,
        dangling_lines=report.dangling_lines,
        insufficient_lines=report.insufficient_lines,
        verified_citations=report.verified_citations,
        dangling_citations=report.dangling_citations,
    )
    if report.has_dangling:
        feedback = (
            f"your previous response cited chunk_ids absent from the context: "
            f"{report.dangling_citations}. Only cite the `id` attribute of <source> "
            "elements that appear in the <sources> block, and copy the matching "
            "document_id and a verbatim evidence span for each."
        )
        with log_stage("citation_retry", request_id, dangling=report.dangling_citations):
            estimate = await _generate(context_block, query, feedback=feedback)
        report = verify_citations(estimate, retrieved_ids)
        if report.has_dangling:
            log.warning(
                "citations_unrepaired",
                request_id=request_id,
                dangling_citations=report.dangling_citations,
            )
            estimate = estimate.model_copy(update={"confidence": "low"})

    # 6.5 Semantic hallucination gate (Session 11): referential integrity is not
    #     entailment. A deterministic numeric anchor + a strict judge grade every
    #     grounded line; a line that claims more than its evidence supports is a
    #     hallucination wearing a real citation → downgrade confidence.
    if runtime_retrieval.effective_hallucination_gate():
        with log_stage("hallucination_gate", request_id):
            gate = await gate_estimate(
                estimate,
                kept,
                tolerance=settings.HALLUCINATION_NUMERIC_TOLERANCE,
                judge_model=settings.HALLUCINATION_JUDGE_MODEL,
            )
        log.info(
            "hallucination_report",
            request_id=request_id,
            total_lines=gate.total_lines,
            grounded_lines=gate.grounded_lines,
            degraded_lines=gate.degraded_lines,
            insufficient_lines=gate.insufficient_lines,
        )
        if gate.has_degraded and estimate.confidence in ("high", "medium"):
            estimate = estimate.model_copy(update={"confidence": "low"})

    # 7. Coherence guard: one repair attempt, then reject.
    if not check_coherence(estimate):
        feedback = (
            'when confidence is "insufficient", total_engineer_days and '
            "duration_weeks must be null, modules must be empty and "
            "insufficient_context_explanation must be filled; otherwise provide "
            "the modules, tasks and numbers."
        )
        with log_stage("coherence_repair", request_id):
            estimate = await _generate(context_block, query, feedback=feedback)
        if not check_coherence(estimate):
            raise MalformedEstimateError(
                "Estimate violates the insufficient-context coherence rule."
            )

    # 8. Persist for idempotent retries.
    if idempotency_key:
        await asyncio.to_thread(store.set, idempotency_key, estimate)

    return estimate
