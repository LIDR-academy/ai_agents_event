"""HTTP layer for semantic search over the persisted corpus (Session 8).

Thin router: validation lives in ``SearchRequest`` (k bounds → 422), the
embed + SQL ranking lives in ``SemanticRetriever``. An empty corpus is a 200
with no results, not an error.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_semantic_retriever
from app.generation.rag.retriever import SemanticRetriever
from app.generation.rag.schemas import SearchRequest, SearchResponse

log = structlog.get_logger()

router = APIRouter(tags=["search"])


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    retriever: SemanticRetriever | None = Depends(get_semantic_retriever),
) -> SearchResponse:
    """Return the k chunks closest to the query by cosine distance."""
    if retriever is None:
        # No OPENAI_API_KEY configured. Generic message to the client, detail logged.
        log.error("search_failed", reason="retriever_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    try:
        return await retriever.search(query=request.query, k=request.k)
    except Exception as exc:  # noqa: BLE001 — embedding/DB failures become a 500.
        log.error(
            "search_failed",
            reason="search_error",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=500, detail="Failed to run semantic search.") from exc
