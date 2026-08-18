"""Unit tests for the advanced-pipeline fusion helpers and config wiring.

Pure logic, no I/O: the differentiated fusion across sub-queries (RRF for
expansion vs round-robin for decomposition), the cross-collection dedup key, and
StageConfig.from_settings overrides. The full async orchestration is exercised
end-to-end in the live-session verification (it wires the real store/reranker).
"""

from __future__ import annotations

from types import SimpleNamespace

from app.generation.rag.retrieval.advanced_pipeline import (
    StageConfig,
    _chunk_key,
    _fuse_collection,
)
from app.generation.rag.retrieval.collections import Collection
from app.generation.rag.retrieval.pipeline import CollectionHits
from app.generation.rag.retrieval.query_transform import DECOMPOSE, EXPAND
from app.generation.rag.schemas import RetrievedChunk


def _chunk(chunk_id: int) -> RetrievedChunk:
    return RetrievedChunk(
        id=chunk_id, content=f"c{chunk_id}", chunk_type="budget_component", distance=0.2
    )


def _hits(vector_ids: list[int]) -> CollectionHits:
    candidates = {cid: _chunk(cid) for cid in vector_ids}
    return CollectionHits(
        collection=Collection.BUDGET,
        candidates=candidates,
        vector_ids=vector_ids,
        lexical_ids=[],
        candidates_evaluated=len(vector_ids),
    )


def test_expansion_fuses_by_consensus_rrf():
    # id 3 is the only one both sub-queries surface → it must win (consensus).
    sq1 = _hits([1, 2, 3])
    sq2 = _hits([3, 4, 5])
    fused = _fuse_collection([sq1, sq2], technique=EXPAND, search_mode="vector", rrf_k=60)
    assert fused[0].id == 3


def test_decomposition_fuses_by_coverage_round_robin():
    sq1 = _hits([1, 2, 3])
    sq2 = _hits([3, 4, 5])
    fused = _fuse_collection([sq1, sq2], technique=DECOMPOSE, search_mode="vector", rrf_k=60)
    # Round-robin interleaves and dedupes: 1, then 3 (sq2 #1), then 2, 4, 5.
    assert [c.id for c in fused] == [1, 3, 2, 4, 5]


def test_chunk_key_separates_collections_with_same_id():
    budget = RetrievedChunk(id=1, content="b", chunk_type="t", distance=0.1, collection="budget")
    transcript = RetrievedChunk(
        id=1, content="t", chunk_type="t", distance=0.1, collection="transcript"
    )
    assert _chunk_key(budget) != _chunk_key(transcript)


def test_stage_config_from_settings_applies_overrides():
    settings = SimpleNamespace(
        RETRIEVAL_ROUTING_ENABLED=True,
        QUERY_TRANSFORM_ENABLED=True,
        TEMPORAL_DECAY_ENABLED=False,
        RERANK_TOP_N=5,
        RETRIEVAL_RECALL_TOP_K=50,
        RETRIEVAL_DISTANCE_THRESHOLD=0.6,
        RRF_K=60,
        TEMPORAL_DECAY_HALF_LIFE_DAYS=900,
        QUERY_MAX_SUBQUERIES=4,
        ROUTER_MAX_TARGETS=3,
        ROUTER_MODEL="gpt-4o-mini",
        QUERY_TRANSFORM_MODEL="gpt-4o-mini",
    )
    stages = StageConfig.from_settings(
        settings,
        search_mode="hybrid",
        rerank=True,
        routing_enabled=False,  # explicit override beats the settings default
        temporal_decay_enabled=True,
        top_k=3,
    )
    assert stages.routing_enabled is False
    assert stages.query_transform_enabled is True  # falls back to settings default
    assert stages.temporal_decay_enabled is True
    assert stages.search_mode == "hybrid"
    assert stages.rerank is True
    assert stages.top_k == 3
    assert stages.recall_k == 50
