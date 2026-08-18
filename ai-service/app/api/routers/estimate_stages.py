"""``POST /v1/estimate/stages/*`` — the RAG pipeline, one stage at a time (S09).

A teaching aid for the live session and the Rails wizard: the full
``/v1/estimate/from-transcript`` pipeline hides its intermediate artifacts (the
reformulated brief, the retrieved chunks, the assembled context block), so this
router exposes each stage as an independent, stateless endpoint. Every endpoint
REUSES the same pure functions the orchestrator uses — no pipeline logic is
re-implemented here.

Contract is stateless: the caller persists each stage's output and passes it
back into the next stage. This keeps server state out of the wizard and lets a
stage be re-run on its own (e.g. retrieval with different filters).

Thin transport, same posture as the sibling routers: validation in the request
schema (422), auth in ``require_estimate_key`` (401), rate limiting in the
decorator (429), pipeline failures → 502. Per-stage logs are correlated by the
``X-Request-ID`` response header.

The estimate key guards the whole wizard (this is the estimation pipeline). The
generate stage deliberately does NOT replicate the orchestrator's corrective
retry loop — it returns the grounding signals (fabricated ids, coherence) so the
UI can show them.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_request_id
from app.api.rate_limiting import limiter
from app.api.security import require_estimate_key
from app.config import get_settings
from app.dependencies import get_embedder, get_token_encoder
from app.generation.rag.context_assembler import build_context_block, truncate_to_token_budget
from app.generation.rag.errors import RagError, RetrievalError
from app.generation.rag.estimator import generate_estimate, generate_structure
from app.generation.rag.quality.augmentation import augment_chunks
from app.generation.rag.quality.hallucination import gate_estimate
from app.generation.rag.observability import log_stage
from app.generation.rag.query_reformulator import compose_search_text, reformulate_query
from app.generation.rag.retriever import search_chunks
from app.generation.rag.schemas import (
    AssembleRequest,
    AssembleResult,
    GenerateRequest,
    GenerateResult,
    HallucinationReport,
    ReformulateRequest,
    ReformulationResult,
    RetrievalRequest,
    RetrievalResult,
    StructureRequest,
    VerifyRequest,
)
from app.generation.rag.validation import check_coherence, verify_citations

log = structlog.get_logger()

router = APIRouter(prefix="/v1/estimate/stages", tags=["estimate-stages"])


@router.post(
    "/reformulate",
    response_model=ReformulationResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("30/minute")
async def reformulate(request: Request, payload: ReformulateRequest) -> ReformulationResult:
    """Stage 1 — distill a transcript into a structured brief + search text."""
    request_id = get_request_id(request)
    try:
        with log_stage("reformulation", request_id):
            query = await reformulate_query(payload.transcript)
            search_text = compose_search_text(query)
        return ReformulationResult(query=query, search_text=search_text)
    except RagError as exc:
        log.error("stage_failed", stage="reformulation", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail="Query reformulation failed.") from exc


@router.post(
    "/retrieve",
    response_model=RetrievalResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("60/minute")
async def retrieve(request: Request, payload: RetrievalRequest) -> RetrievalResult:
    """Stage 2 — embed the search text and run metadata-filtered k-NN.

    Reuses ``RetrievalRequest`` (the search text is passed as ``query_text``)
    and returns the same ``RetrievalResult`` as ``/v1/retrieval/search`` so the
    wizard sees ``low_confidence``/``candidates_evaluated`` for the soft-fail
    branch."""
    request_id = get_request_id(request)
    embedder = get_embedder()
    if embedder is None:
        log.error("stage_failed", stage="retrieval", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    try:
        with log_stage("retrieval", request_id, sectors=payload.sectors):
            query_embedding = await asyncio.to_thread(embedder.embed_one, payload.query_text)
            return await search_chunks(
                query_embedding,
                top_k=payload.top_k,
                distance_threshold=payload.distance_threshold,
                sectors=payload.sectors,
                project_year_min=payload.project_year_min,
                project_year_max=payload.project_year_max,
                chunk_types=payload.chunk_types,
            )
    except RetrievalError as exc:
        raise HTTPException(status_code=502, detail="Retrieval failed.") from exc
    except Exception as exc:  # noqa: BLE001 — embedding/other failures → 502.
        log.error("stage_failed", stage="retrieval", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail="Failed to run retrieval.") from exc


@router.post(
    "/assemble",
    response_model=AssembleResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("60/minute")
async def assemble(request: Request, payload: AssembleRequest) -> AssembleResult:
    """Stage 3 — truncate to the token budget (whole chunks) and build the
    ``<source>`` XML context block."""
    request_id = get_request_id(request)
    settings = get_settings()
    budget = payload.max_context_tokens or settings.MAX_CONTEXT_TOKENS
    encoder = get_token_encoder()

    with log_stage("augmentation", request_id, budget=budget, augment=payload.augment):
        kept = truncate_to_token_budget(payload.chunks, budget, encoder)
        if payload.augment:
            kept = augment_chunks(kept, compress=payload.compress, reorder=payload.reorder)
        context_block = build_context_block(kept)
        token_count = len(encoder.encode(context_block))

    return AssembleResult(
        context_block=context_block,
        kept_chunks=kept,
        dropped_count=len(payload.chunks) - len(kept),
        token_count=token_count,
        augmented=payload.augment,
    )


@router.post(
    "/structure",
    response_model=GenerateResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("15/minute")
async def structure(request: Request, payload: StructureRequest) -> GenerateResult:
    """Session 10 — generate the module→task structure as a FREE decomposition of
    the brief (no retrieval, no sources). Returns the same ``GenerateResult`` shape
    as ``/generate`` (citations always clean) so the wizard parses it unchanged."""
    request_id = get_request_id(request)
    try:
        with log_stage("structure", request_id):
            estimate = await generate_structure(payload.query)
    except RagError as exc:
        log.error("stage_failed", stage="structure", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail="Structure generation failed.") from exc

    return GenerateResult(estimate=estimate, fabricated_source_ids=[], coherent=True)


@router.post(
    "/generate",
    response_model=GenerateResult,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("15/minute")
async def generate(request: Request, payload: GenerateRequest) -> GenerateResult:
    """Stage 4 — generate the grounded estimate and report grounding signals.

    Unlike the full pipeline, this does not auto-retry on fabricated citations
    or incoherence; it returns ``fabricated_source_ids`` and ``coherent`` so the
    wizard can surface them as a teaching moment."""
    request_id = get_request_id(request)
    try:
        with log_stage(
            "generation",
            request_id,
            sources=len(payload.kept_chunks),
            include_hours=payload.include_hours,
        ):
            estimate = await generate_estimate(
                payload.context_block,
                structured_query=payload.query,
                include_hours=payload.include_hours,
            )
    except RagError as exc:
        log.error("stage_failed", stage="generation", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail="Estimate generation failed.") from exc

    retrieved_ids = {str(chunk.id) for chunk in payload.kept_chunks}
    report = verify_citations(estimate, retrieved_ids)
    coherent = check_coherence(estimate)
    return GenerateResult(
        estimate=estimate,
        fabricated_source_ids=report.dangling_citations,
        citation_report=report,
        coherent=coherent,
    )


@router.post(
    "/verify",
    response_model=HallucinationReport,
    dependencies=[Depends(require_estimate_key)],
)
@limiter.limit("15/minute")
async def verify(request: Request, payload: VerifyRequest) -> HallucinationReport:
    """Stage 5 (Session 11) — semantic hallucination gate over a generated estimate.

    Where ``/generate`` returns REFERENTIAL grounding (a cited id was retrieved),
    this returns SEMANTIC grounding: a deterministic numeric anchor + a strict
    judge grade each line grounded / degraded / insufficient. Reuses the same pure
    functions the full pipeline runs at step 6.5 — no logic is re-implemented."""
    request_id = get_request_id(request)
    settings = get_settings()
    try:
        with log_stage("hallucination_gate", request_id, use_judge=payload.use_judge):
            return await gate_estimate(
                payload.estimate,
                payload.kept_chunks,
                tolerance=settings.HALLUCINATION_NUMERIC_TOLERANCE,
                judge_model=settings.HALLUCINATION_JUDGE_MODEL,
                use_judge=payload.use_judge,
            )
    except RagError as exc:
        log.error("stage_failed", stage="hallucination_gate", error_type=type(exc).__name__)
        raise HTTPException(status_code=502, detail="Hallucination gate failed.") from exc
