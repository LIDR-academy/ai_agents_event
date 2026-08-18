"""``POST /v1/retrieval/search`` — metadata-filtered semantic retrieval (S09).

Canonical search endpoint: it embeds the query text and runs k-NN with a
relevance threshold plus optional structural filters (sector / project year /
chunk type). It supersedes the unauthenticated Session 8 ``POST /search``, which
remains only for backwards compatibility (Chunking Lab / S08 demos).

Thin transport: validation lives in ``RetrievalRequest`` (422), auth in
``require_retrieval_key`` (401), rate limiting in the ``@limiter`` decorator
(429). Soft-fail (nothing crosses the threshold) is a 200 with
``low_confidence=true``, not an error.
"""

from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.rate_limiting import limiter
from app.api.security import require_retrieval_key
from app.config import get_settings
from app.dependencies import get_embedder, get_runtime_retrieval_config
from app.generation.rag.errors import RetrievalError
from app.generation.rag.retrieval.pipeline import retrieve
from app.generation.rag.schemas import RetrievalRequest, RetrievalResult

log = structlog.get_logger()

router = APIRouter(prefix="/v1/retrieval", tags=["retrieval"])


@router.post(
    "/search",
    response_model=RetrievalResult,
    dependencies=[Depends(require_retrieval_key)],
)
@limiter.limit("120/minute")
async def search(request: Request, payload: RetrievalRequest) -> RetrievalResult:
    """Return the best chunks for the query under the resolved search mode.

    ``search_mode``/``rerank`` precedence: explicit request field → runtime
    override (Ajustes UI) → .env default. ``distance_threshold`` still enforces
    the vector relevance floor (and the soft-fail when nothing is retrieved)."""
    embedder = get_embedder()
    if embedder is None:
        log.error("retrieval_failed", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    settings = get_settings()
    runtime = get_runtime_retrieval_config()
    search_mode = payload.search_mode or runtime.effective_search_mode()
    rerank = payload.rerank if payload.rerank is not None else runtime.effective_rerank()

    try:
        query_embedding = await asyncio.to_thread(embedder.embed_one, payload.query_text)
        return await retrieve(
            query_embedding=query_embedding,
            query_text=payload.query_text,
            search_mode=search_mode,
            rerank=rerank,
            top_k=payload.top_k,
            recall_k=settings.RETRIEVAL_RECALL_TOP_K,
            rerank_top_n=settings.RERANK_TOP_N,
            distance_threshold=payload.distance_threshold,
            rrf_k=settings.RRF_K,
            sectors=payload.sectors,
            project_year_min=payload.project_year_min,
            project_year_max=payload.project_year_max,
            chunk_types=payload.chunk_types,
        )
    except RetrievalError as exc:
        raise HTTPException(status_code=502, detail="Retrieval failed.") from exc
    except Exception as exc:  # noqa: BLE001 — embedding/other failures → 502.
        log.error(
            "retrieval_failed",
            reason="search_error",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to run retrieval.") from exc
