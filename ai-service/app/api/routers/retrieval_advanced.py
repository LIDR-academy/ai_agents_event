"""``POST /v1/retrieval/advanced-search`` — multi-index advanced retrieval (S10).

The Session 10 counterpart of ``/v1/retrieval/search``: it adds query
transform (expansion/decomposition), cascade routing across the three
collections, hard metadata filters, differentiated fusion, reranking and
temporal decay — every stage independently switchable. The Session 9 endpoint is
left untouched for backwards compatibility.

Thin transport: auth in ``require_retrieval_key`` (401), rate limiting in the
``@limiter`` decorator (429), validation in the request model (422). The response
surfaces not just the chunks but HOW they were obtained (routing level + reason,
technique, sub-queries, per-collection cardinality) so the live session can show
each decision.
"""

from __future__ import annotations

from datetime import date, datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.rate_limiting import limiter
from app.api.security import require_retrieval_key
from app.config import get_settings
from app.dependencies import get_embedder, get_runtime_retrieval_config
from app.generation.rag.errors import RetrievalError
from app.generation.rag.retrieval.advanced_pipeline import StageConfig, advanced_retrieve
from app.generation.rag.retrieval.collections import Collection, HardFilters
from app.generation.rag.schemas import RetrievedChunk

log = structlog.get_logger()

router = APIRouter(prefix="/v1/retrieval", tags=["retrieval"])


class AdvancedRetrievalRequest(BaseModel):
    """Payload for ``POST /v1/retrieval/advanced-search``."""

    query_text: str = Field(min_length=10, max_length=2000)
    # Level-0 routing: name the collections and the router classifies nothing.
    collections: list[Collection] | None = Field(
        default=None, description="Explicit collections to search (skips routing)."
    )
    top_k: int = Field(default=5, ge=1, le=30)
    # Hard metadata filters (embedded in the search query, Article 6).
    technologies: list[str] | None = None
    sectors: list[str] | None = None
    min_date: date | None = Field(
        default=None, description="Exclude chunks older than this (budgets: by year)."
    )
    doc_version: str | None = Field(default=None, description="Technical-doc version filter.")
    # Per-request stage overrides (None ⇒ fall back to runtime/settings).
    search_mode: str | None = Field(default=None, description="'vector' or 'hybrid'.")
    rerank: bool | None = None
    routing_enabled: bool | None = None
    query_transform_enabled: bool | None = None
    temporal_decay_enabled: bool | None = None


class RoutingInfo(BaseModel):
    level: str
    targets: list[str]
    reason: str


class SubQueryInfo(BaseModel):
    topic: str
    query: str


class AdvancedRetrievalResult(BaseModel):
    """Results plus the diagnostics that explain how they were retrieved."""

    chunks: list[RetrievedChunk]
    low_confidence: bool
    routing: RoutingInfo
    technique: str
    subqueries: list[SubQueryInfo]
    cardinality: dict[str, int] = Field(
        description="Candidates per collection after hard filters (watch for 0 = silent empty)."
    )


@router.post(
    "/advanced-search",
    response_model=AdvancedRetrievalResult,
    dependencies=[Depends(require_retrieval_key)],
)
@limiter.limit("120/minute")
async def advanced_search(
    request: Request, payload: AdvancedRetrievalRequest
) -> AdvancedRetrievalResult:
    """Run the advanced multi-index pipeline; precedence for search_mode/rerank is
    explicit request field → runtime override (Ajustes UI) → .env default."""
    embedder = get_embedder()
    if embedder is None:
        log.error("advanced_retrieval_failed", reason="embedder_unavailable")
        raise HTTPException(status_code=500, detail="Embedding service is not available.")

    settings = get_settings()
    runtime = get_runtime_retrieval_config()
    # Precedence for every toggle: explicit request field → runtime override → .env.
    search_mode = payload.search_mode or runtime.effective_search_mode()
    rerank = payload.rerank if payload.rerank is not None else runtime.effective_rerank()
    routing_enabled = (
        payload.routing_enabled
        if payload.routing_enabled is not None
        else runtime.effective_routing()
    )
    query_transform_enabled = (
        payload.query_transform_enabled
        if payload.query_transform_enabled is not None
        else runtime.effective_query_transform()
    )
    temporal_decay_enabled = (
        payload.temporal_decay_enabled
        if payload.temporal_decay_enabled is not None
        else runtime.effective_temporal_decay()
    )

    stages = StageConfig.from_settings(
        settings,
        search_mode=search_mode,
        rerank=rerank,
        routing_enabled=routing_enabled,
        query_transform_enabled=query_transform_enabled,
        temporal_decay_enabled=temporal_decay_enabled,
        top_k=payload.top_k,
    )
    hard_filters = HardFilters(
        technologies=tuple(payload.technologies or ()),
        sectors=tuple(payload.sectors or ()),
        min_date=payload.min_date,
        version=payload.doc_version,
    )

    try:
        outcome = await advanced_retrieve(
            query_text=payload.query_text,
            embedder=embedder,
            stages=stages,
            explicit_collections=payload.collections,
            hard_filters=hard_filters,
            reference_date=datetime.now().date(),
        )
    except RetrievalError as exc:
        raise HTTPException(status_code=502, detail="Retrieval failed.") from exc
    except Exception as exc:  # noqa: BLE001
        log.error(
            "advanced_retrieval_failed",
            reason="search_error",
            error_type=type(exc).__name__,
            error=str(exc)[:300],
        )
        raise HTTPException(status_code=502, detail="Failed to run advanced retrieval.") from exc

    return AdvancedRetrievalResult(
        chunks=outcome.chunks,
        low_confidence=outcome.low_confidence,
        routing=RoutingInfo(
            level=outcome.routing.level,
            targets=[t.value for t in outcome.routing.targets],
            reason=outcome.routing.reason,
        ),
        technique=outcome.technique,
        subqueries=[SubQueryInfo(topic=sq.topic, query=sq.query) for sq in outcome.subqueries],
        cardinality=outcome.cardinality,
    )
