"""Unit tests for the Session 10 recall-then-rerank pipeline (``retrieve``).

The vector store, lexical branch and cross-encoder are all faked: no Postgres,
no torch. We assert the four configurations (vector/hybrid × rerank on/off)
compose correctly — recall width, RRF fusion of the two branches, the
reranker reordering + top-N cut, and the soft-fail contract.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.dependencies as deps
from app.generation.rag.retrieval.pipeline import retrieve


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _factory():
    return FakeSession()


def _row(chunk_id: int, *, distance: float = 0.3, budget_id: str | None = None):
    return SimpleNamespace(
        id=chunk_id,
        document_id=chunk_id * 10,
        chunk_type="budget_component",
        content=f"chunk-{chunk_id}",
        metadata_={
            "client_sector": "ecommerce",
            "year": 2024,
            "budget_id": budget_id or f"BUD-{chunk_id}",
        },
        distance=distance,
    )


class FakeStore:
    def __init__(self, vector_rows, lexical_rows, candidates=12):
        self.vector_rows = vector_rows
        self.lexical_rows = lexical_rows
        self.candidates = candidates
        self.vector_calls: list[dict] = []
        self.lexical_calls: list[dict] = []

    async def search_filtered(self, session, **kwargs):
        self.vector_calls.append(kwargs)
        return self.vector_rows[: kwargs["top_k"]], self.candidates

    async def search_lexical(self, session, **kwargs):
        self.lexical_calls.append(kwargs)
        return self.lexical_rows[: kwargs["top_k"]]


class FakeReranker:
    """Reorders candidates by a content→score map and cuts to top_n."""

    def __init__(self, scores_by_content):
        self.scores_by_content = scores_by_content

    def rerank(self, query, candidates, *, top_n, text_of=lambda c: c.content):
        ranked = sorted(candidates, key=lambda c: self.scores_by_content[text_of(c)], reverse=True)
        return ranked[:top_n]


@pytest.fixture
def wire(monkeypatch):
    def _wire(store):
        monkeypatch.setattr(deps, "get_async_session_factory", lambda: _factory)
        monkeypatch.setattr(deps, "get_chunk_store", lambda: store)

    return _wire


async def test_vector_mode_no_rerank_returns_distance_order(wire):
    store = FakeStore(vector_rows=[_row(1), _row(2), _row(3)], lexical_rows=[])
    wire(store)

    result = await retrieve(
        query_embedding=[0.0] * 1536,
        query_text="ecommerce checkout",
        search_mode="vector",
        rerank=False,
        top_k=2,
    )

    assert [c.id for c in result.chunks] == [1, 2]
    assert result.low_confidence is False
    assert result.candidates_evaluated == 12
    assert result.chunks[0].budget_id == "BUD-1"
    # Pure vector / no rerank recalls exactly top_k (no wide recall).
    assert store.vector_calls[0]["top_k"] == 2
    assert store.lexical_calls == []  # lexical branch not touched


async def test_hybrid_mode_fuses_vector_and_lexical(wire):
    # Vector finds 1,2; lexical finds 3 (only the keyword branch surfaces it).
    store = FakeStore(
        vector_rows=[_row(1), _row(2)],
        lexical_rows=[_row(3), _row(1)],
    )
    wire(store)

    result = await retrieve(
        query_embedding=[0.0] * 1536,
        query_text="ecommerce checkout",
        search_mode="hybrid",
        rerank=False,
        top_k=5,
        recall_k=50,
    )

    ids = {c.id for c in result.chunks}
    assert ids == {1, 2, 3}  # lexical-only id 3 made it into the fused result
    # Hybrid recalls wide on both branches.
    assert store.vector_calls[0]["top_k"] == 50
    assert store.lexical_calls[0]["top_k"] == 50


async def test_rerank_reorders_and_cuts_to_top_n(wire):
    store = FakeStore(
        vector_rows=[_row(1), _row(2), _row(3)],
        lexical_rows=[],
    )
    wire(store)
    # Make chunk-3 the most relevant per the cross-encoder, chunk-1 the least.
    reranker = FakeReranker({"chunk-1": 0.1, "chunk-2": 0.5, "chunk-3": 0.9})

    result = await retrieve(
        query_embedding=[0.0] * 1536,
        query_text="ecommerce checkout",
        search_mode="vector",
        rerank=True,
        recall_k=50,
        rerank_top_n=2,
        reranker=reranker,
    )

    assert [c.id for c in result.chunks] == [3, 2]  # reranked order, cut to 2


async def test_soft_fail_when_nothing_retrieved(wire):
    store = FakeStore(vector_rows=[], lexical_rows=[], candidates=7)
    wire(store)

    result = await retrieve(
        query_embedding=[0.0] * 1536,
        query_text="ecommerce checkout",
        search_mode="hybrid",
        rerank=False,
    )

    assert result.chunks == []
    assert result.low_confidence is True
    assert result.candidates_evaluated == 7
