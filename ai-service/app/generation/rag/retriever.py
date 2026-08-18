"""Semantic retriever over the pgvector store (Session 8).

Embeds the query with the SAME model used at ingest time (mixing embedding
models makes distances meaningless) and ranks chunks by cosine distance via
SQL. No vector index and no metadata filtering yet — both are built live in
the session on top of this baseline.
"""

from __future__ import annotations

import asyncio
import time

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.generation.rag.embedding.embedder import OpenAIEmbedder
from app.generation.rag.errors import RetrievalError
from app.generation.rag.schemas import (
    RetrievalResult,
    RetrievedChunk,
    SearchHit,
    SearchResponse,
)
from app.generation.rag.store.repository import ChunkStore

log = structlog.get_logger()


class SemanticRetriever:
    """k-NN retrieval: embed the query, rank chunks by cosine distance."""

    def __init__(
        self,
        embedder: OpenAIEmbedder,
        session_factory: async_sessionmaker,
        store: ChunkStore,
    ) -> None:
        self._embedder = embedder
        self._session_factory = session_factory
        self._store = store

    async def search(self, *, query: str, k: int) -> SearchResponse:
        started = time.perf_counter()

        # Sync OpenAI client → thread, same reasoning as in the ingest path.
        query_vector = await asyncio.to_thread(self._embedder.embed_one, query)

        async with self._session_factory() as session:
            rows = await self._store.search(session, query_vector=query_vector, k=k)

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        response = SearchResponse(
            query=query,
            k=k,
            search_time_ms=elapsed_ms,
            results=[
                SearchHit(
                    chunk_id=row.id,
                    document_id=row.document_id,
                    chunk_type=row.chunk_type,
                    content=row.content,
                    distance=float(row.distance),
                    metadata=row.metadata_,
                )
                for row in rows
            ],
        )
        log.info(
            "rag_search_done",
            query=query[:80],
            k=k,
            results=len(response.results),
            search_time_ms=elapsed_ms,
        )
        return response


async def search_chunks(
    query_embedding: list[float],
    top_k: int = 10,
    distance_threshold: float = 0.6,
    sectors: list[str] | None = None,
    project_year_min: int | None = None,
    project_year_max: int | None = None,
    chunk_types: list[str] | None = None,
) -> RetrievalResult:
    """Metadata-filtered k-NN retrieval with a relevance threshold (Session 9).

    The query is assumed already embedded (the orchestrator embeds the composed
    search text). Structural filters narrow the space before the vector ranking;
    the threshold enforces a relevance floor. Reuses the shared async session
    factory and :class:`ChunkStore` from the composition root.

    Parameters
    ----------
    query_embedding:
        Dense query vector (1536-dim, same model as ingest).
    top_k, distance_threshold:
        Locked defaults 10 / 0.6 (cosine distance, lower = closer).
    sectors, project_year_min, project_year_max, chunk_types:
        Optional structural filters; ``None`` means "do not filter on this axis".

    Returns
    -------
    RetrievalResult
        ``chunks`` ascending by distance. ``low_confidence`` is True (soft-fail)
        when nothing crosses the threshold — the orchestrator then skips
        generation instead of grounding on irrelevant budgets.

    Raises
    ------
    RetrievalError
        If the vector store cannot be queried.
    """
    from app.dependencies import get_async_session_factory, get_chunk_store

    session_factory = get_async_session_factory()
    store = get_chunk_store()

    started = time.perf_counter()
    try:
        async with session_factory() as session:
            rows, candidates_evaluated = await store.search_filtered(
                session,
                query_vector=query_embedding,
                top_k=top_k,
                distance_threshold=distance_threshold,
                sectors=sectors,
                project_year_min=project_year_min,
                project_year_max=project_year_max,
                chunk_types=chunk_types,
            )
    except Exception as exc:  # noqa: BLE001 — DB/connection failure.
        log.error("rag_filtered_search_failed", error_type=type(exc).__name__, error=str(exc)[:200])
        raise RetrievalError("Vector store query failed.") from exc

    chunks = [
        RetrievedChunk(
            id=row.id,
            content=row.content,
            sector=str(row.metadata_.get("client_sector", "unknown")),
            project_year=int(row.metadata_.get("year", 0)),
            chunk_type=row.chunk_type,
            distance=float(row.distance),
            budget_id=row.metadata_.get("budget_id"),
            estimated_hours=row.metadata_.get("estimated_hours"),
        )
        for row in rows
    ]
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "rag_filtered_search_done",
        results=len(chunks),
        candidates_evaluated=candidates_evaluated,
        top_k=top_k,
        distance_threshold=distance_threshold,
        search_time_ms=elapsed_ms,
    )
    return RetrievalResult(
        chunks=chunks,
        low_confidence=not chunks,
        candidates_evaluated=candidates_evaluated,
    )
