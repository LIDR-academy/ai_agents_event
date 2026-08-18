"""Advanced multi-index retrieval pipeline (Session 10).

Assembles every advanced technique into the order Article 6 establishes —
"lo barato y excluyente, al principio; lo caro y fino, al final; lo blando, al
cierre":

    query transform → routing → hard filters → hybrid search → fusion
                    → reranking → temporal decay → top-k

Every stage is gated by :class:`StageConfig`, so the pipeline is the MAXIMUM path,
not the mandatory one, and each technique can be measured in isolation. The
endpoint builds a ``StageConfig`` from settings/runtime; the measurement harness
builds named ``StageConfig``s as DATA.

Fusion is differentiated by technique (Article 4): expansion variants are fused by
consensus (RRF), decomposition sub-queries by coverage (round-robin). Across
collections the merge is round-robin too, keyed by ``(collection, id)`` because
chunk ids only collide-free WITHIN a collection. Every chunk keeps its provenance
label.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import date

import structlog

from app.config import Settings
from app.generation.rag.errors import RetrievalError
from app.generation.rag.retrieval.collections import (
    Collection,
    HardFilters,
    spec_for,
)
from app.generation.rag.retrieval.fusion import reciprocal_rank_fusion, round_robin_merge
from app.generation.rag.retrieval.pipeline import CollectionHits, hybrid_search_one
from app.generation.rag.retrieval.query_transform import (
    DECOMPOSE,
    SubQuery,
    transform_query,
)
from app.generation.rag.retrieval.router import RoutingDecision, route
from app.generation.rag.retrieval.temporal import apply_temporal_decay
from app.generation.rag.schemas import RetrievedChunk

log = structlog.get_logger()


@dataclass(frozen=True)
class StageConfig:
    """Which advanced stages run, plus their parameters. Pure data (no I/O)."""

    routing_enabled: bool = True
    query_transform_enabled: bool = True
    search_mode: str = "hybrid"
    rerank: bool = True
    temporal_decay_enabled: bool = False
    top_k: int = 5
    recall_k: int = 50
    distance_threshold: float = 2.0  # permissive: ranking quality, not the soft-fail gate
    rrf_k: int = 60
    half_life_days: int = 900
    max_subqueries: int = 4
    max_targets: int = 3
    router_model: str | None = None
    query_transform_model: str | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        search_mode: str,
        rerank: bool,
        routing_enabled: bool | None = None,
        query_transform_enabled: bool | None = None,
        temporal_decay_enabled: bool | None = None,
        top_k: int | None = None,
        distance_threshold: float | None = None,
    ) -> "StageConfig":
        """Build the effective config from settings, with explicit overrides."""
        return cls(
            routing_enabled=settings.RETRIEVAL_ROUTING_ENABLED
            if routing_enabled is None
            else routing_enabled,
            query_transform_enabled=settings.QUERY_TRANSFORM_ENABLED
            if query_transform_enabled is None
            else query_transform_enabled,
            search_mode=search_mode,
            rerank=rerank,
            temporal_decay_enabled=settings.TEMPORAL_DECAY_ENABLED
            if temporal_decay_enabled is None
            else temporal_decay_enabled,
            top_k=top_k or settings.RERANK_TOP_N,
            recall_k=settings.RETRIEVAL_RECALL_TOP_K,
            distance_threshold=settings.RETRIEVAL_DISTANCE_THRESHOLD
            if distance_threshold is None
            else distance_threshold,
            rrf_k=settings.RRF_K,
            half_life_days=settings.TEMPORAL_DECAY_HALF_LIFE_DAYS,
            max_subqueries=settings.QUERY_MAX_SUBQUERIES,
            max_targets=settings.ROUTER_MAX_TARGETS,
            router_model=settings.ROUTER_MODEL,
            query_transform_model=settings.QUERY_TRANSFORM_MODEL,
        )


@dataclass
class AdvancedRetrievalOutcome:
    """Everything the endpoint surfaces: results + how they were obtained."""

    chunks: list[RetrievedChunk]
    routing: RoutingDecision
    technique: str
    subqueries: list[SubQuery]
    cardinality: dict[str, int] = field(default_factory=dict)
    low_confidence: bool = True


def _chunk_key(chunk: RetrievedChunk) -> tuple[str, int]:
    """Cross-collection dedup identity (ids are unique only within a collection)."""
    return (chunk.collection, chunk.id)


def _sigmoid(value: float) -> float:
    """Map a cross-encoder logit to a non-negative (0,1) score so temporal decay
    (a 0..1 multiplier) preserves ordering instead of inverting negatives."""
    return 1.0 / (1.0 + math.exp(-value))


def _fuse_collection(
    hits_per_subquery: list[CollectionHits], *, technique: str, search_mode: str, rrf_k: int
) -> list[RetrievedChunk]:
    """Fuse one collection's per-sub-query results into a single ordered list.

    Expansion/direct → RRF (consensus across reformulations); decomposition →
    round-robin (coverage across topics).
    """
    candidates: dict[int, RetrievedChunk] = {}
    for hits in hits_per_subquery:
        candidates.update(hits.candidates)

    ordered_per_subquery = [
        hits.fused_order(search_mode=search_mode, rrf_k=rrf_k) for hits in hits_per_subquery
    ]

    if technique == DECOMPOSE:
        return round_robin_merge(ordered_per_subquery, key=lambda chunk: chunk.id)

    # EXPAND / DIRECT: consensus by position across the sub-query orderings.
    fused = reciprocal_rank_fusion(
        [[chunk.id for chunk in ordered] for ordered in ordered_per_subquery], k=rrf_k
    )
    return [candidates[cid] for cid, _score in fused]


async def advanced_retrieve(
    *,
    query_text: str,
    embedder,
    stages: StageConfig,
    explicit_collections: list[Collection] | None = None,
    hard_filters: HardFilters | None = None,
    reference_date: date,
    reranker=None,
) -> AdvancedRetrievalOutcome:
    """Run the full advanced pipeline and return results + diagnostics."""
    from app.dependencies import get_async_session_factory, get_chunk_store

    session_factory = get_async_session_factory()
    store = get_chunk_store()
    hard_filters = hard_filters or HardFilters()

    # Stage 1+2 — query transform and routing (independent, run concurrently).
    plan, routing = await asyncio.gather(
        transform_query(
            query_text,
            enabled=stages.query_transform_enabled,
            model=stages.query_transform_model,
            max_subqueries=stages.max_subqueries,
        ),
        route(
            query_text,
            explicit=explicit_collections,
            rules_enabled=stages.routing_enabled,
            classifier_enabled=stages.routing_enabled,
            max_targets=stages.max_targets,
            model=stages.router_model,
        ),
    )

    # Embed every sub-query once (parallel; embedding is a shared OpenAI round-trip).
    try:
        embeddings = await asyncio.gather(
            *(asyncio.to_thread(embedder.embed_one, sq.query) for sq in plan.subqueries)
        )
    except Exception as exc:  # noqa: BLE001
        log.error("advanced_embed_failed", error_type=type(exc).__name__, error=str(exc)[:200])
        raise RetrievalError("Failed to embed sub-queries.") from exc

    # Stage 3+4 — hard filters + hybrid search, per (collection, sub-query),
    # each in its own session so the fan-out is genuinely parallel and safe.
    async def _search(collection: Collection, sq: SubQuery, emb: list[float]) -> CollectionHits:
        spec = spec_for(collection)
        extra_filters = spec.hard_filter_clauses(hard_filters)
        async with session_factory() as session:
            return await hybrid_search_one(
                session,
                store,
                collection=collection,
                query_embedding=emb,
                query_text=sq.query,
                search_mode=stages.search_mode,
                recall_k=stages.recall_k,
                top_k=stages.top_k,
                distance_threshold=stages.distance_threshold,
                will_rerank=stages.rerank,
                extra_filters=extra_filters,
            )

    try:
        flat = await asyncio.gather(
            *(
                _search(collection, sq, emb)
                for collection in routing.targets
                for sq, emb in zip(plan.subqueries, embeddings)
            )
        )
    except Exception as exc:  # noqa: BLE001
        log.error("advanced_search_failed", error_type=type(exc).__name__, error=str(exc)[:200])
        raise RetrievalError("Vector store query failed.") from exc

    # Group the flat results back by collection (sub-queries are contiguous).
    per_collection: dict[Collection, list[CollectionHits]] = {c: [] for c in routing.targets}
    index = 0
    for collection in routing.targets:
        for _sq in plan.subqueries:
            per_collection[collection].append(flat[index])
            index += 1

    # Stage 5 — fusion within each collection, then round-robin across collections
    # (coverage; provenance preserved). Log cardinality after the hard filters.
    cardinality: dict[str, int] = {}
    collection_orders: list[list[RetrievedChunk]] = []
    for collection, hits_list in per_collection.items():
        cardinality[collection.value] = hits_list[0].candidates_evaluated if hits_list else 0
        log.info(
            "advanced_collection_cardinality",
            collection=collection.value,
            candidates_evaluated=cardinality[collection.value],
        )
        ordered = _fuse_collection(
            hits_list, technique=plan.technique, search_mode=stages.search_mode, rrf_k=stages.rrf_k
        )
        if ordered:
            collection_orders.append(ordered)

    pool = round_robin_merge(collection_orders, key=_chunk_key)[: stages.recall_k]

    # Stage 6 — reranking (cross-encoder scores) or a positional base score.
    if stages.rerank and pool:
        if reranker is None:
            from app.dependencies import get_reranker

            reranker = get_reranker()
        reranked = await asyncio.to_thread(
            reranker.rerank_with_scores, query_text, pool, top_n=len(pool)
        )
        # Sigmoid the logits → non-negative base for the (multiplicative) decay.
        scored = [(chunk, _sigmoid(score)) for chunk, score in reranked]
    else:
        scored = [(chunk, 1.0 / (position + 1)) for position, chunk in enumerate(pool)]

    # Stage 7 — temporal decay (soft, last), then the final top-k cut.
    if stages.temporal_decay_enabled:
        scored = apply_temporal_decay(
            scored, half_life_days=stages.half_life_days, reference_date=reference_date
        )
    final_scored = scored[: stages.top_k]
    final = [chunk.model_copy(update={"relevance_score": score}) for chunk, score in final_scored]

    log.info(
        "advanced_retrieve_done",
        technique=plan.technique,
        routing_level=routing.level,
        targets=[t.value for t in routing.targets],
        subqueries=len(plan.subqueries),
        pool=len(pool),
        results=len(final),
    )
    return AdvancedRetrievalOutcome(
        chunks=final,
        routing=routing,
        technique=plan.technique,
        subqueries=plan.subqueries,
        cardinality=cardinality,
        low_confidence=not final,
    )
