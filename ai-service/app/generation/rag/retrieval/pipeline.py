"""Recall-then-rerank retrieval pipeline (Session 10).

The single ``retrieve()`` entrypoint composes the four configurations the exercise
measures, behind two switches the caller resolves (param → runtime → settings):

* ``search_mode="vector"`` — dense k-NN only (the Session 9 behaviour).
* ``search_mode="hybrid"`` — dense + lexical branches fused with RRF.
* ``rerank=False`` — keep the top ``top_k`` of the recall ordering.
* ``rerank=True``  — recall WIDE (``recall_k``, e.g. 50) then let the cross-encoder
  rescore and keep the top ``rerank_top_n`` (e.g. 5). The recall stage's only job
  is not to lose the relevant document; the reranker's job is to float it up.

It returns the same :class:`RetrievalResult` as ``search_chunks`` so every existing
consumer (orchestrator, stage endpoints) is unaffected.

The single-collection primitive ``hybrid_search_one()`` is factored out so the
Session 10 *advanced* pipeline (multi-index routing, query expansion) can reuse
the exact same recall+fusion logic across collections and sub-queries.
"""

from __future__ import annotations

import asyncio
import time

import structlog

from app.generation.rag.errors import RetrievalError
from app.generation.rag.retrieval.collections import Collection, spec_for
from app.generation.rag.retrieval.fusion import reciprocal_rank_fusion
from app.generation.rag.schemas import RetrievalResult, RetrievedChunk

log = structlog.get_logger()

# Distance assigned to a candidate surfaced ONLY by the lexical branch (it never
# entered the vector ranking, so it has no cosine distance). 1.0 = "far" in cosine
# terms; it is a display/sentinel value — fusion and reranking ignore it.
_NO_VECTOR_DISTANCE = 1.0


class CollectionHits:
    """Recall output for one (collection, query): candidates + branch rankings.

    The candidate pool maps ``chunk_id → RetrievedChunk`` (ids are unique WITHIN a
    collection). ``vector_ids``/``lexical_ids`` are the per-branch orderings the
    caller fuses (RRF for expansion variants, round-robin for decomposition).
    """

    def __init__(
        self,
        *,
        collection: Collection,
        candidates: dict[int, RetrievedChunk],
        vector_ids: list[int],
        lexical_ids: list[int],
        candidates_evaluated: int,
    ) -> None:
        self.collection = collection
        self.candidates = candidates
        self.vector_ids = vector_ids
        self.lexical_ids = lexical_ids
        self.candidates_evaluated = candidates_evaluated

    def fused_order(self, *, search_mode: str, rrf_k: int) -> list[RetrievedChunk]:
        """Chunks best-first: RRF of both branches for hybrid, distance for vector."""
        if search_mode == "hybrid":
            fused = reciprocal_rank_fusion([self.vector_ids, self.lexical_ids], k=rrf_k)
            return [self.candidates[cid] for cid, _score in fused]
        return [self.candidates[cid] for cid in self.vector_ids]


async def hybrid_search_one(
    session,
    store,
    *,
    collection: Collection,
    query_embedding: list[float],
    query_text: str,
    search_mode: str,
    recall_k: int,
    top_k: int,
    distance_threshold: float,
    will_rerank: bool,
    sectors: list[str] | None = None,
    project_year_min: int | None = None,
    project_year_max: int | None = None,
    chunk_types: list[str] | None = None,
    extra_filters: list | None = None,
) -> CollectionHits:
    """Run the vector (and, for hybrid, lexical) branches over ONE collection.

    Recall wide whenever a later stage (fusion or rerank) will re-sort; recall
    exactly ``top_k`` only for the plain vector path where the order is final.
    """
    spec = spec_for(collection)
    wide = will_rerank or search_mode == "hybrid"
    vector_limit = recall_k if wide else top_k

    vector_rows, candidates_evaluated = await store.search_filtered(
        session,
        model=spec.model,
        query_vector=query_embedding,
        top_k=vector_limit,
        distance_threshold=distance_threshold,
        sectors=sectors,
        project_year_min=project_year_min,
        project_year_max=project_year_max,
        chunk_types=chunk_types,
        extra_filters=extra_filters,
    )
    lexical_rows = []
    if search_mode == "hybrid":
        lexical_rows = await store.search_lexical(
            session,
            model=spec.model,
            query_text=query_text,
            top_k=recall_k,
            sectors=sectors,
            project_year_min=project_year_min,
            project_year_max=project_year_max,
            chunk_types=chunk_types,
            extra_filters=extra_filters,
        )

    candidates: dict[int, RetrievedChunk] = {}
    for row in vector_rows:
        candidates[row.id] = spec.to_chunk(row, distance=float(row.distance))
    for row in lexical_rows:
        candidates.setdefault(row.id, spec.to_chunk(row, distance=_NO_VECTOR_DISTANCE))

    return CollectionHits(
        collection=collection,
        candidates=candidates,
        vector_ids=[row.id for row in vector_rows],
        lexical_ids=[row.id for row in lexical_rows],
        candidates_evaluated=candidates_evaluated,
    )


async def retrieve(
    *,
    query_embedding: list[float],
    query_text: str,
    search_mode: str = "vector",
    rerank: bool = False,
    top_k: int = 10,
    recall_k: int = 50,
    rerank_top_n: int = 5,
    distance_threshold: float = 0.6,
    rrf_k: int = 60,
    collection: Collection = Collection.BUDGET,
    sectors: list[str] | None = None,
    project_year_min: int | None = None,
    project_year_max: int | None = None,
    chunk_types: list[str] | None = None,
    extra_filters: list | None = None,
    reranker=None,
) -> RetrievalResult:
    """Run hybrid/vector retrieval with optional cross-encoder reranking.

    Single-collection path (defaults to budgets), unchanged for every Session 9
    consumer. ``query_text`` is required for the lexical branch and the reranker
    even when ``search_mode`` is ``"vector"``. ``reranker`` is injectable for
    tests; when ``None`` and ``rerank`` is True it is pulled from the composition
    root.

    Returns the top results best-first (cross-encoder order when reranking, else
    distance/RRF order). ``low_confidence`` is True (soft-fail) when nothing was
    retrieved at all — the orchestrator then short-circuits to an
    insufficient-context estimate instead of grounding on noise.
    """
    from app.dependencies import get_async_session_factory, get_chunk_store

    session_factory = get_async_session_factory()
    store = get_chunk_store()
    started = time.perf_counter()

    try:
        async with session_factory() as session:
            hits = await hybrid_search_one(
                session,
                store,
                collection=collection,
                query_embedding=query_embedding,
                query_text=query_text,
                search_mode=search_mode,
                recall_k=recall_k,
                top_k=top_k,
                distance_threshold=distance_threshold,
                will_rerank=rerank,
                sectors=sectors,
                project_year_min=project_year_min,
                project_year_max=project_year_max,
                chunk_types=chunk_types,
                extra_filters=extra_filters,
            )
    except Exception as exc:  # noqa: BLE001 — DB/connection failure.
        log.error("rag_hybrid_search_failed", error_type=type(exc).__name__, error=str(exc)[:200])
        raise RetrievalError("Vector store query failed.") from exc

    ordered = hits.fused_order(search_mode=search_mode, rrf_k=rrf_k)

    if rerank and ordered:
        if reranker is None:
            from app.dependencies import get_reranker

            reranker = get_reranker()
        recall_pool = ordered[:recall_k]
        final = await asyncio.to_thread(
            reranker.rerank, query_text, recall_pool, top_n=rerank_top_n
        )
    else:
        final = ordered[:top_k]

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    log.info(
        "rag_retrieve_done",
        collection=collection.value,
        search_mode=search_mode,
        rerank=rerank,
        vector_hits=len(hits.vector_ids),
        lexical_hits=len(hits.lexical_ids),
        results=len(final),
        candidates_evaluated=hits.candidates_evaluated,
        search_time_ms=elapsed_ms,
    )
    return RetrievalResult(
        chunks=final,
        low_confidence=not final,
        candidates_evaluated=hits.candidates_evaluated,
    )
