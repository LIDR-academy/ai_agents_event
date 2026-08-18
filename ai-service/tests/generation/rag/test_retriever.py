"""Unit tests for metadata-filtered retrieval (Session 9).

The vector store is faked: no Postgres. We assert the soft-fail contract and the
metadata→schema flattening (``client_sector``/``year`` → ``sector``/``project_year``).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.dependencies as deps
from app.generation.rag.errors import RetrievalError
from app.generation.rag.retriever import search_chunks


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _factory():
    return FakeSession()


def _row(chunk_id: int, distance: float) -> SimpleNamespace:
    return SimpleNamespace(
        id=chunk_id,
        document_id=chunk_id * 10,
        chunk_type="budget_component",
        content="Cart and checkout service",
        metadata_={"client_sector": "ecommerce", "year": 2024, "budget_id": "BUD-2024-005"},
        distance=distance,
    )


class FakeStore:
    def __init__(self, rows, candidates):
        self._rows = rows
        self._candidates = candidates
        self.calls: list[dict] = []

    async def search_filtered(self, session, **kwargs):
        self.calls.append(kwargs)
        return self._rows, self._candidates


@pytest.fixture
def wire(monkeypatch):
    def _wire(store):
        monkeypatch.setattr(deps, "get_async_session_factory", lambda: _factory)
        monkeypatch.setattr(deps, "get_chunk_store", lambda: store)

    return _wire


async def test_search_chunks_returns_hits_above_threshold(wire):
    store = FakeStore(rows=[_row(1, 0.41), _row(2, 0.55)], candidates=12)
    wire(store)

    result = await search_chunks(
        [0.0] * 1536, top_k=10, distance_threshold=0.6, sectors=["ecommerce"]
    )

    assert result.low_confidence is False
    assert result.candidates_evaluated == 12
    assert [c.id for c in result.chunks] == [1, 2]
    first = result.chunks[0]
    assert first.sector == "ecommerce"  # flattened from client_sector
    assert first.project_year == 2024  # flattened from year
    assert first.chunk_type == "budget_component"
    # The structural filter was forwarded to the store.
    assert store.calls[0]["sectors"] == ["ecommerce"]
    assert store.calls[0]["distance_threshold"] == 0.6


async def test_search_chunks_soft_fail_when_nothing_crosses_threshold(wire):
    store = FakeStore(rows=[], candidates=12)
    wire(store)

    result = await search_chunks([0.0] * 1536, distance_threshold=0.2)

    assert result.chunks == []
    assert result.low_confidence is True
    assert result.candidates_evaluated == 12


async def test_search_chunks_wraps_store_failure_in_retrieval_error(wire):
    class ExplodingStore(FakeStore):
        async def search_filtered(self, session, **kwargs):
            raise RuntimeError("connection refused")

    wire(ExplodingStore(rows=[], candidates=0))

    with pytest.raises(RetrievalError):
        await search_chunks([0.0] * 1536)
