"""HTTP layer for the embedding pipeline.

Thin router: it maps service exceptions to status codes. The chunk → embed →
persist orchestration lives in ``RagIngestService``; no business logic here.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.dependencies import (
    ALL_STRATEGIES,
    build_chunkers,
    get_embedder,
    get_rag_ingest_service,
)
from app.generation.rag.analysis.comparison import (
    ChunkingComparator,
    CompareRequest,
    CompareResponse,
)
from app.generation.rag.embedding.embedder import OpenAIEmbedder
from app.generation.rag.ingest_service import DuplicateDocumentError, RagIngestService
from app.generation.rag.schemas import IngestRequest, IngestResponse

log = structlog.get_logger()

router = APIRouter(prefix="/embeddings", tags=["embeddings"])


@router.post(
    "/ingest",
    response_model=IngestResponse,
    responses={409: {"description": "Document already ingested"}},
)
async def ingest(
    request: IngestRequest,
    service: RagIngestService | None = Depends(get_rag_ingest_service),
) -> IngestResponse | JSONResponse:
    """Persist one budget as a document + embedded chunks (one transaction)."""
    if service is None:
        # No OPENAI_API_KEY configured. Generic message to the client, detail logged.
        log.error("embeddings_ingest_failed", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    log.info(
        "embeddings_ingest_received",
        source_path=request.source_path,
        document_type=request.document_type,
    )
    try:
        return await service.ingest(
            source_path=request.source_path,
            document_type=request.document_type,
            budget=request.content,
            chunk_type=request.chunk_type,
        )
    except DuplicateDocumentError as exc:
        # JSONResponse (not HTTPException) to keep the exercise's literal
        # top-level shape: {"detail": ..., "document_id": ...}.
        log.info(
            "embeddings_ingest_duplicate",
            source_path=request.source_path,
            document_id=exc.document_id,
        )
        return JSONResponse(
            status_code=409,
            content={"detail": "Document already ingested", "document_id": exc.document_id},
        )
    except Exception as exc:  # noqa: BLE001 — embedding/DB failures become a 500.
        log.error(
            "embeddings_ingest_failed",
            reason="ingest_error",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=500, detail="Failed to generate embeddings.") from exc


@router.post("/compare", response_model=CompareResponse)
def compare(
    request: CompareRequest,
    embedder: OpenAIEmbedder | None = Depends(get_embedder),
) -> CompareResponse:
    """Run several chunking strategies over the same budgets and compare them.

    Returns per-strategy corpus stats and, if queries are given, the top-k
    chunks each strategy retrieves. Nothing is persisted (Session 8 territory).
    """
    if embedder is None:
        log.error("embeddings_compare_failed", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    names = request.strategies or ALL_STRATEGIES
    try:
        chunkers = build_chunkers(names)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {exc.args[0]}") from exc
    except RuntimeError as exc:
        # A strategy needs an API key that is not configured.
        log.error("embeddings_compare_failed", reason="missing_api_key", error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    comparator = ChunkingComparator(chunkers, embedder)
    log.info(
        "embeddings_compare_received",
        total_budgets=len(request.budgets),
        strategies=names,
        n_queries=len(request.queries),
    )
    try:
        stats = comparator.compute_stats(request.budgets)
        queries = comparator.run_queries(request.budgets, request.queries, request.top_k)
    except Exception as exc:  # noqa: BLE001 — any chunker/embedding failure becomes a 500.
        log.error(
            "embeddings_compare_failed",
            reason="comparison_error",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=500, detail="Failed to run chunking comparison.") from exc

    return CompareResponse(stats_per_strategy=stats, queries_per_strategy=queries)
